import asyncio
import logging
import time
import uuid

import httpx

from app.config import settings
from app.schemas import HospitalCreate
from app.services.batch_store import batches
from app.utils.text import normalize

logger = logging.getLogger(__name__)

HOSPITAL_API_URL = settings.hospital_api_url

# The external API runs on Render's free tier and can take up to a minute
# to wake up, so the defaults allow generous timeouts and a few retries
# with backoff. All of these can be changed with environment variables.
REQUEST_TIMEOUT = httpx.Timeout(
    settings.request_timeout_seconds,
    connect=settings.connect_timeout_seconds,
)
MAX_ATTEMPTS = settings.max_attempts
RETRY_BASE_DELAY = settings.retry_base_delay_seconds  # doubles per attempt

# Gateway errors and rate limiting mean the request was not processed,
# so they are safe to retry even for POST.
SAFE_RETRY_STATUSES = {429, 502, 503, 504}


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
            retryable = status in SAFE_RETRY_STATUSES or (idempotent and status >= 500)
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

        delay = RETRY_BASE_DELAY * 2 ** (attempt - 1)
        logger.warning(
            "%s %s failed (attempt %d/%d), retrying in %.1fs",
            method.upper(),
            url,
            attempt,
            MAX_ATTEMPTS,
            delay,
        )
        await asyncio.sleep(delay)

    raise RuntimeError("unreachable")


async def warm_up(client: httpx.AsyncClient, batch_id: str) -> None:
    """Wake the external API before sending concurrent requests.

    One cheap read absorbs a cold start, so the batch's POST requests
    don't all time out together. Failures are ignored: the real requests
    have their own retries.
    """
    logger.debug("Warming up external API")
    try:
        await send_with_retry(
            client,
            "get",
            f"{HOSPITAL_API_URL}/hospitals/batch/{batch_id}",
            idempotent=True,
        )
    except httpx.HTTPError as exc:
        logger.warning("Warm-up request failed, continuing anyway: %s", exc)


# ---------------------------------------------------------------------------
# Errors raised to the API layer
# ---------------------------------------------------------------------------


class BatchError(Exception):
    """Base class for batch errors; status_code maps to the HTTP response."""

    status_code = 400


class BatchNotFound(BatchError):
    status_code = 404

    def __init__(self) -> None:
        super().__init__("Batch not found")


class BatchBusy(BatchError):
    status_code = 409

    def __init__(self) -> None:
        super().__init__("Batch is still being processed; try again when it finishes")


class NothingToRetry(BatchError):
    status_code = 400


class UpstreamUnavailable(BatchError):
    status_code = 502


# Batches with work in progress (initial run or a retry). asyncio runs one
# coroutine at a time, so checking and adding to this set without an await
# in between is atomic within the process.
_busy_batches: set[str] = set()


def public_error(exc: Exception) -> str:
    """Short, client-safe description of an upstream failure.

    The full exception is logged; clients only see a summary, so internal
    URLs and raw library messages are not exposed.
    """
    if isinstance(exc, httpx.HTTPStatusError):
        return f"Hospital Directory API returned HTTP {exc.response.status_code}"
    if isinstance(exc, httpx.TimeoutException):
        return "Timed out waiting for the Hospital Directory API"
    if isinstance(exc, httpx.TransportError):
        return "Could not reach the Hospital Directory API"
    return "Unexpected response from the Hospital Directory API"


def build_payload(row: dict, batch_id: str) -> dict:
    """JSON body for POST /hospitals/ on the Hospital Directory API."""
    return HospitalCreate(
        name=row["name"].strip(),
        address=row["address"].strip(),
        phone=(row.get("phone") or "").strip() or None,
        creation_batch_id=batch_id,
    ).model_dump()


def parse_hospital_id(response: httpx.Response) -> int:
    """Read the new hospital's ID, rejecting unexpected response shapes."""
    data = response.json()
    if not isinstance(data, dict) or not isinstance(data.get("id"), int):
        raise ValueError(f"Unexpected create response: {data!r:.200}")
    return data["id"]


def recount(batch: dict) -> None:
    """Derive the counters from the per-row results, so they never drift."""
    failed = sum(result["status"] == "failed" for result in batch["hospitals"])
    succeeded = sum(result["status"] != "failed" for result in batch["hospitals"])
    batch["failed_hospitals"] = failed
    batch["processed_hospitals"] = succeeded


def input_row(batch: dict, row_number: int) -> dict:
    """Find the uploaded CSV row for a result's row number."""
    for position, row in enumerate(batch["hospitals_input"], start=1):
        if row.get("row", position) == row_number:
            return row
    raise KeyError(row_number)


def batch_summary(batch: dict) -> dict:
    return {k: v for k, v in batch.items() if k != "hospitals_input"}


def evict_old_batches() -> None:
    """Keep the in-memory store bounded by dropping the oldest finished batches."""
    excess = len(batches) - settings.max_stored_batches
    if excess <= 0:
        return

    for batch_id in list(batches):
        if excess <= 0:
            break
        if batch_id in _busy_batches:
            continue
        if batches[batch_id].get("status") in ("completed", "failed"):
            del batches[batch_id]
            excess -= 1


def create_batch(hospitals: list[dict]) -> str:
    """Register a new batch in the store and return its ID."""
    evict_old_batches()
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
    # Busy from creation, so a retry can't start while a background run
    # is queued but hasn't begun yet.
    _busy_batches.add(batch_id)

    logger.info("Created batch %s with %d hospitals", batch_id, len(hospitals))
    return batch_id


async def create_one(
    client: httpx.AsyncClient, batch_id: str, row_number: int, row: dict
) -> dict:
    """Create one hospital upstream and return its result entry."""
    payload = build_payload(row, batch_id)

    try:
        response = await send_with_retry(
            client,
            "post",
            f"{HOSPITAL_API_URL}/hospitals/",
            idempotent=False,
            json=payload,
        )
        hospital_id = parse_hospital_id(response)

    except Exception as exc:  # one bad row must never stop the batch
        logger.error(
            "Batch %s: row %d (%s) failed: %r",
            batch_id,
            row_number,
            payload["name"],
            exc,
        )
        return {
            "row": row_number,
            "name": payload["name"],
            "status": "failed",
            "error": public_error(exc),
        }

    logger.debug(
        "Batch %s: row %d created (hospital_id=%s)", batch_id, row_number, hospital_id
    )
    return {
        "row": row_number,
        "hospital_id": hospital_id,
        "name": payload["name"],
        "status": "created",
    }


async def activate(client: httpx.AsyncClient, batch: dict) -> None:
    """Activate the batch upstream if, and only if, every row was created."""
    batch_id = batch["batch_id"]
    recount(batch)

    if batch["failed_hospitals"] or len(batch["hospitals"]) != batch["total_hospitals"]:
        return

    batch["status"] = "activating"
    logger.info("Batch %s: all hospitals created, activating", batch_id)

    try:
        await send_with_retry(
            client,
            "patch",
            f"{HOSPITAL_API_URL}/hospitals/batch/{batch_id}/activate",
            idempotent=True,
        )
    except Exception as exc:
        batch["activation_error"] = public_error(exc)
        logger.error("Batch %s: activation failed: %r", batch_id, exc)
        return

    batch["batch_activated"] = True
    batch.pop("activation_error", None)
    for result in batch["hospitals"]:
        result["status"] = "created_and_activated"


def finish(batch: dict, start_time: float) -> None:
    batch["processing_time_seconds"] = round(time.perf_counter() - start_time, 2)
    batch["status"] = "completed" if batch["batch_activated"] else "failed"

    if batch["batch_activated"]:
        logger.info(
            "Batch %s: completed in %.2fs",
            batch["batch_id"],
            batch["processing_time_seconds"],
        )
    else:
        logger.warning(
            "Batch %s: finished with %d failed hospital(s), not activated",
            batch["batch_id"],
            batch["failed_hospitals"],
        )


async def run_batch(batch_id: str) -> dict:
    """Create all hospitals of a batch, updating progress live."""
    batch = batches[batch_id]
    start_time = time.perf_counter()
    logger.info("Batch %s: processing started", batch_id)

    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            await warm_up(client, batch_id)

            async def create_and_publish(position: int, row: dict) -> dict:
                result = await create_one(
                    client, batch_id, row.get("row", position), row
                )
                # Publish progress as soon as each hospital finishes.
                batch["hospitals"].append(result)
                recount(batch)
                batch["processing_time_seconds"] = round(
                    time.perf_counter() - start_time, 2
                )
                return result

            results = await asyncio.gather(
                *[
                    create_and_publish(position, row)
                    for position, row in enumerate(batch["hospitals_input"], start=1)
                ]
            )

            # Keep final results in CSV row order.
            batch["hospitals"] = list(results)
            await activate(client, batch)

    except Exception as exc:
        # Never leave a batch stuck in "processing".
        logger.exception("Batch %s: unexpected error", batch_id)
        batch["error"] = (
            f"Unexpected error while processing batch: {type(exc).__name__}"
        )

    finally:
        recount(batch)
        finish(batch, start_time)
        _busy_batches.discard(batch_id)

    return batch_summary(batch)


async def process_hospitals(hospitals: list[dict]) -> dict:
    """Create a batch and process it, waiting for the full result."""
    batch_id = create_batch(hospitals)
    return await run_batch(batch_id)


async def retry_failed_hospitals(batch_id: str) -> dict:
    """Retry failed rows (or a failed activation) of a finished batch.

    Raises BatchError subclasses for requests that can't be retried.
    """
    batch = batches.get(batch_id)

    if batch is None:
        raise BatchNotFound()

    # Refuse while the first run or another retry is still working on this
    # batch. Otherwise a retry could activate the batch before every row
    # exists, or two retries could both re-create the same row.
    if batch_id in _busy_batches or batch.get("status") in ("processing", "activating"):
        raise BatchBusy()

    if batch["batch_activated"]:
        raise NothingToRetry("Batch is already activated")

    failed_results = [
        result for result in batch["hospitals"] if result["status"] == "failed"
    ]

    if not failed_results and "activation_error" not in batch:
        raise NothingToRetry("No failed hospitals to retry")

    _busy_batches.add(batch_id)
    start_time = time.perf_counter()
    logger.info(
        "Batch %s: retrying %d failed hospital(s)%s",
        batch_id,
        len(failed_results),
        "" if failed_results else " (activation only)",
    )

    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            if failed_results:
                await reconcile_and_recreate(client, batch, failed_results)
            await activate(client, batch)
    finally:
        recount(batch)
        batch["status"] = "completed" if batch["batch_activated"] else "failed"
        logger.info(
            "Batch %s: retry finished in %.2fs (activated=%s)",
            batch_id,
            time.perf_counter() - start_time,
            batch["batch_activated"],
        )
        _busy_batches.discard(batch_id)

    return batch_summary(batch)


async def reconcile_and_recreate(
    client: httpx.AsyncClient, batch: dict, failed_results: list[dict]
) -> None:
    """Re-create failed rows, skipping any the upstream already has.

    A POST can succeed upstream even if its response never reached us, so
    the batch's existing hospitals are fetched first and matched by
    normalized name and address.
    """
    batch_id = batch["batch_id"]

    try:
        response = await send_with_retry(
            client,
            "get",
            f"{HOSPITAL_API_URL}/hospitals/batch/{batch_id}",
            idempotent=True,
        )
        existing_hospitals = response.json()
        if not isinstance(existing_hospitals, list):
            raise ValueError("Batch lookup did not return a list")
    except Exception as exc:
        logger.error(
            "Batch %s: could not fetch existing hospitals, retry aborted: %r",
            batch_id,
            exc,
        )
        raise UpstreamUnavailable(
            "Could not verify existing batch hospitals; no retries were attempted"
        ) from exc

    existing_by_key = {
        (normalize(h.get("name", "")), normalize(h.get("address", ""))): h
        for h in existing_hospitals
        if isinstance(h, dict)
    }

    async def retry_row(result: dict) -> None:
        row = input_row(batch, result["row"])
        existing = existing_by_key.get(
            (normalize(row["name"]), normalize(row["address"]))
        )

        if existing:
            logger.info(
                "Batch %s: row %d already exists upstream (hospital_id=%s), "
                "skipping re-create",
                batch_id,
                result["row"],
                existing.get("id"),
            )
            new_result = {
                "row": result["row"],
                "hospital_id": existing.get("id"),
                "name": result["name"],
                "status": "created",
            }
        else:
            new_result = await create_one(client, batch_id, result["row"], row)

        result.clear()
        result.update(new_result)

    # Retried rows run in parallel, like the first run.
    await asyncio.gather(*[retry_row(result) for result in failed_results])
