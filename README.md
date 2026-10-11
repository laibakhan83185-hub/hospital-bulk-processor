# Hospital Bulk Processing API

Bulk upload service for the Paribus Hospital Directory API. You upload a CSV of hospitals, it creates them in the directory under one batch, and activates the batch once every hospital has been created.

Live: https://hospital-bulk-processor-7hw2.onrender.com/docs

Note: this runs on Render's free tier, so the first request after some idle time can take up to a minute while the service wakes up.

## Running locally

Needs Python 3.13+.

```bash
git clone https://github.com/laibakhan83185-hub/hospital-bulk-processor.git
cd hospital-bulk-processor
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Then open http://localhost:8000/docs.

With Docker:

```bash
docker compose up --build
```

or without compose:

```bash
docker build -t hospital-bulk-processor .
docker run --rm -p 8000:8000 hospital-bulk-processor
```

## CSV format

```csv
name,address,phone
City Hospital,MG Road,9876543210
County Hospital,Park Street,
```

`name` and `address` are required, `phone` is optional. Up to 20 hospitals per file. Duplicate rows (same name and address, ignoring case and extra spaces) are rejected. Column names are case-insensitive, blank lines are skipped, and any column other than these three is treated as an error.

## Endpoints

### POST /hospitals/bulk

Uploads the CSV and processes it. All hospitals are created in parallel with the same batch ID, and if every one succeeds the batch is activated. If any creation fails the batch is left inactive so it can be retried.

```bash
curl -F "file=@hospitals.csv" https://hospital-bulk-processor-7hw2.onrender.com/hospitals/bulk
```

Response from the live deployment:

```json
{
  "batch_id": "310ec15a-a3ad-4cfc-bf0f-0483243a11de",
  "status": "completed",
  "total_hospitals": 2,
  "processed_hospitals": 2,
  "failed_hospitals": 0,
  "processing_time_seconds": 27.67,
  "batch_activated": true,
  "hospitals": [
    {"row": 1, "hospital_id": 1, "name": "Test Hospital A", "status": "created_and_activated"},
    {"row": 2, "hospital_id": 2, "name": "Test Hospital B", "status": "created_and_activated"}
  ]
}
```

(Most of those 27 seconds was the external API waking up. When it's already running a batch takes a couple of seconds.)

If the CSV is invalid you get a 400 listing all the problems in the file.

Add `?background=true` to get the batch ID back right away and let processing continue in the background. You can then poll the status endpoint below.

### GET /hospitals/bulk/{batch_id}

Current state of a batch. Counts and per-row results update as each hospital finishes, so this works for tracking progress. `status` goes `processing` → `activating` → `completed`, or `failed` if a creation or the activation failed.

### POST /hospitals/bulk/{batch_id}/retry

Retries the failed rows of a batch, or just the activation if that was what failed. Before re-sending a hospital it checks whether the external API already has it in the batch, since a request can succeed on their side even if the response never reached us. That avoids creating duplicates.

### POST /hospitals/bulk/validate

Checks a CSV without creating anything. Returns every error at once with its line number:

```json
{
  "valid": false,
  "total_hospitals": 0,
  "error_count": 2,
  "errors": [
    {"row": 2, "message": "Hospital name is missing"},
    {"row": 3, "message": "Duplicate hospital found (same as row 2)"}
  ],
  "message": "CSV validation failed"
}
```

### GET /health

Returns `{"status": "ok"}`. Used as the health check on Render.

## How it works / decisions

Hospitals are created concurrently with `asyncio.gather` and `httpx`, so a batch takes about as long as the slowest single request instead of the sum of all of them. With a 20-row limit there was no need to cap concurrency.

Activation only happens when every row succeeded. Since batch hospitals start out inactive, a half-finished upload never shows up as live data.

The external API also sleeps on the free tier, which caused timeouts on the first request. To handle that, the service sends one request to wake it up before starting a batch, and retries failed calls up to 3 times with backoff (1s, then 2s). GET and PATCH are safe to repeat, so they retry on any temporary error. POST is only retried when it's clear the request was never processed (connection errors, 429, 502, 503, 504). After a timeout or a 500 the hospital might already exist, so the row is marked failed and left for the retry endpoint, which checks first.

`POST /hospitals/bulk` waits for the full result by default, to match the response format in the assignment. Background mode is opt-in.

Batch state is kept in memory, which the assignment allows.

## Models

Request and response shapes are Pydantic models in `app/schemas.py`, so they show up with full schemas in `/docs`.

- `HospitalRow` validates each CSV row: strips whitespace, requires name and address, and checks the phone format. The CSV parser collects these errors for every row instead of stopping at the first.
- `HospitalCreate` is the payload sent to the Hospital Directory API.
- `BatchResponse`, `HospitalResult` and `ValidationResponse` are the response models. Their model validators check that the data is consistent before it's returned, for example that processed + failed never exceeds the total, a batch with failures is never marked activated, and `valid` matches the error count.

## Logging

Uses Python's `logging` module. Batch start and finish are logged at INFO, retries and unactivated batches at WARNING, and failed hospitals or activations at ERROR. Every line includes the batch ID, so a batch can be followed from start to finish.

The level is set with the `LOG_LEVEL` environment variable (default `INFO`). `LOG_LEVEL=DEBUG` also logs each created hospital and the outgoing HTTP requests.

## Tests

```bash
python -m pytest -v
```

The external API is mocked. Tests cover CSV validation, partial failures, failed activation, retries and duplicate handling, the backoff rules, background mode and progress updates.

## Limitations

- Batches are stored in memory, so they're lost on restart, including any that were still running in the background.
- The retry check matches hospitals by name and address. It makes duplicates much less likely but can't fully guarantee exactly-once creation.
- Everything depends on the external API being available.

## Project layout

```
app/
  main.py                      endpoints
  schemas.py                   request and response models
  logging_config.py            logging setup
  services/hospital_service.py batch processing, retries, external API calls
  services/batch_store.py      in-memory batch storage
  utils/csv_utils.py           CSV parsing and validation
tests/
```

External API docs: https://hospital-directory.onrender.com/docs
